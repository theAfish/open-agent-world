"""Google ADK 2.x adapter behind the application's AgentRuntime boundary."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
from collections.abc import AsyncIterator, Mapping
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
    AgentStateError,
    AgentStatus,
)
from .tools import build_scoped_tool_callables


@dataclass(frozen=True, slots=True)
class _AdkBindings:
    Agent: Any
    App: Any
    Runner: Any
    InMemorySessionService: Any
    types: Any


def _load_adk_bindings() -> _AdkBindings:
    try:
        version = importlib.metadata.version("google-adk")
        major, minor, *_ = (int(part) for part in version.split(".")[:2])
        if major != 2 or minor < 8:
            raise AgentDependencyError(
                f"Google ADK >=2.8,<3 is required; installed version is {version}"
            )
        from google.adk.agents import Agent
        from google.adk.apps import App
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService
        from google.genai import types
    except (ImportError, importlib.metadata.PackageNotFoundError) as exc:
        raise AgentDependencyError(
            "Google ADK runtime selected but google-adk>=2.8,<3 is not installed"
        ) from exc
    return _AdkBindings(Agent, App, Runner, InMemorySessionService, types)


class GoogleAdkAgentRuntime(AgentRuntime):
    """One independent ADK session per Agent Card.

    Tools are reconstructed from the graph at the beginning of every run.  Each
    generated callable also delegates back to the capability provider at call
    time, so a capability snapshot is never an authorization token.
    """

    def __init__(
        self,
        capability_provider: AgentCapabilityProvider,
        *,
        app_name: str = "open-agent-world",
        adk_bindings: _AdkBindings | None = None,
    ) -> None:
        self._provider = capability_provider
        self._app_name = app_name
        self._adk = adk_bindings or _load_adk_bindings()
        self._sessions = self._adk.InMemorySessionService()
        self._records: dict[str, AgentRecord] = {}
        self._records_lock = asyncio.Lock()

    async def create_agent(self, config: AgentConfig) -> AgentInfo:
        validate_agent_config(config)
        async with self._records_lock:
            if config.agent_id in self._records:
                raise AgentStateError(f"agent already exists: {config.agent_id}")
            session = await self._sessions.create_session(
                app_name=self._app_name,
                user_id=self._user_id(config.agent_id),
                session_id=self._session_id(config.agent_id),
            )
            record = AgentRecord(config=config, session_id=session.id)
            self._records[config.agent_id] = record
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
        await self._sessions.delete_session(
            app_name=self._app_name,
            user_id=self._user_id(agent_id),
            session_id=record.session_id,
        )
        async with self._records_lock:
            if self._records.get(agent_id) is record:
                del self._records[agent_id]

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
            run_id = f"adk-{agent_id}-{record.run_counter}"
            record.status = AgentStatus.RUNNING
            record.active_run_id = run_id
            record.active_task = task
            record.last_error = None

        yield AgentEvent(agent_id, run_id, AgentEventType.STARTED, {"model": record.config.model})
        yield self._status_event(record, run_id)
        final_text = ""
        try:
            definitions = tuple(await self._provider.list_tools(agent_id))
            tools = build_scoped_tool_callables(self._provider, agent_id, definitions)
            agent = self._adk.Agent(
                name=self._adk_agent_name(agent_id),
                description=record.config.name,
                model=record.config.model,
                instruction=record.config.system_instruction,
                tools=tools,
            )
            app = self._adk.App(name=self._app_name, root_agent=agent)
            message = self._adk.types.Content(
                role="user",
                parts=[self._adk.types.Part.from_text(text=prompt.strip())],
            )
            async with self._adk.Runner(
                app=app, session_service=self._sessions
            ) as runner:
                async for event in runner.run_async(
                    user_id=self._user_id(agent_id),
                    session_id=record.session_id,
                    new_message=message,
                ):
                    async for translated in self._translate_event(
                        record, run_id, event
                    ):
                        if translated.type == AgentEventType.MESSAGE:
                            final_text = str(translated.payload.get("text", final_text))
                        yield translated

            async with record.lock:
                record.status = AgentStatus.IDLE
                record.active_run_id = None
                record.active_task = None
            yield AgentEvent(
                agent_id,
                run_id,
                AgentEventType.COMPLETED,
                {"text": final_text},
            )
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
            yield AgentEvent(
                agent_id, run_id, AgentEventType.ERROR, {"error": str(exc)}
            )
            yield self._status_event(record, run_id)
            raise

    async def _translate_event(
        self, record: AgentRecord, run_id: str, event: Any
    ) -> AsyncIterator[AgentEvent]:
        content = getattr(event, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            function_call = getattr(part, "function_call", None)
            if function_call is not None:
                record.status = AgentStatus.WAITING
                yield self._status_event(record, run_id)
                yield AgentEvent(
                    record.config.agent_id,
                    run_id,
                    AgentEventType.TOOL_STARTED,
                    {
                        "name": function_call.name,
                        "call_id": getattr(function_call, "id", None),
                        "arguments": _json_safe(getattr(function_call, "args", {})),
                    },
                )
            function_response = getattr(part, "function_response", None)
            if function_response is not None:
                yield AgentEvent(
                    record.config.agent_id,
                    run_id,
                    AgentEventType.TOOL_COMPLETED,
                    {
                        "name": function_response.name,
                        "call_id": getattr(function_response, "id", None),
                        "response": _json_safe(
                            getattr(function_response, "response", {})
                        ),
                    },
                )
                record.status = AgentStatus.RUNNING
                yield self._status_event(record, run_id)
            # ADK marks model reasoning parts.  Never forward them to runtime
            # events even when they carry text.
            text = getattr(part, "text", None)
            if text and not bool(getattr(part, "thought", False)):
                is_final = bool(event.is_final_response())
                yield AgentEvent(
                    record.config.agent_id,
                    run_id,
                    AgentEventType.MESSAGE,
                    {"text": text, "final": is_final},
                )

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
    def _digest(agent_id: str) -> str:
        return hashlib.sha256(agent_id.encode("utf-8")).hexdigest()[:24]

    @classmethod
    def _adk_agent_name(cls, agent_id: str) -> str:
        return f"agent_{cls._digest(agent_id)}"

    @classmethod
    def _user_id(cls, agent_id: str) -> str:
        return f"agent-user-{cls._digest(agent_id)}"

    @classmethod
    def _session_id(cls, agent_id: str) -> str:
        return f"agent-session-{cls._digest(agent_id)}"


def _json_safe(value: Any, *, max_length: int = 8192) -> Any:
    """Keep operational event payloads serializable and reasonably bounded."""

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

