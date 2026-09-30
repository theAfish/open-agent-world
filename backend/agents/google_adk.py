"""Google ADK 2.x adapter behind the RuntimeProvider boundary."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
from collections.abc import AsyncIterator, Mapping
from contextlib import aclosing
from dataclasses import dataclass
from typing import Any

from ._state import AgentRecord, validate_agent_config
from .base import AgentCapabilityProvider, RuntimeProvider
from .context import ContextBudget, ContextStore
from .model_observation import ModelTrace, model_stream_disconnected, multiplex_adk_events
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
)
from .tools import build_scoped_tool_callables
from backend.runs.models import InvocationContext, RunStatus, RuntimeInput


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


class GoogleAdkAgentRuntime(RuntimeProvider):
    """Independent ADK state per Agent and optional conversation context.

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
        model_connections: Any = None,
        context_store: ContextStore | None = None,
    ) -> None:
        self._provider = capability_provider
        self._app_name = app_name
        self._adk = adk_bindings or _load_adk_bindings()
        self._sessions = self._adk.InMemorySessionService()
        self._records: dict[str, AgentRecord] = {}
        self._context_sessions: dict[tuple[str, str], str] = {}
        self._records_lock = asyncio.Lock()
        self._litellm_connection: dict[str, str] = {}
        self.model_connections = model_connections
        self.context_store = context_store

    def configure_litellm_connection(
        self, *, api_base: str | None = None, api_key: str | None = None
    ) -> None:
        """Keep ADK LiteLLM connection overrides in runtime memory only."""

        if api_base:
            self._litellm_connection["api_base"] = api_base
        else:
            self._litellm_connection.pop("api_base", None)
        if api_key:
            self._litellm_connection["api_key"] = api_key
        else:
            self._litellm_connection.pop("api_key", None)

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
        session_ids = [record.session_id]
        session_ids.extend(
            value
            for (candidate_id, _), value in self._context_sessions.items()
            if candidate_id == agent_id
        )
        for session_id in session_ids:
            await self._sessions.delete_session(
                app_name=self._app_name,
                user_id=self._user_id(agent_id),
                session_id=session_id,
            )
        async with self._records_lock:
            if self._records.get(agent_id) is record:
                del self._records[agent_id]
            self._context_sessions = {
                key: value
                for key, value in self._context_sessions.items()
                if key[0] != agent_id
            }

    async def execute(
        self,
        config: AgentConfig,
        context: InvocationContext,
        runtime_input: RuntimeInput,
    ) -> AsyncIterator[AgentEvent]:
        if self.context_store is None:
            async with aclosing(self._execute(config, context, runtime_input)) as events:
                async for event in events:
                    yield event
            return
        # Different sessions may run together; one session x Agent must serialize
        # checkpoint reads/updates even if the Agent permits concurrent Runs.
        async with self.context_store.lock(context.agent_id, context.context_id or ""):
            async with aclosing(self._execute(config, context, runtime_input)) as events:
                async for event in events:
                    yield event

    async def _execute(
        self,
        config: AgentConfig,
        context: InvocationContext,
        runtime_input: RuntimeInput,
        _network_retry: int = 0,
    ) -> AsyncIterator[AgentEvent]:
        agent_id = context.agent_id
        prompt = runtime_input.prompt
        if not isinstance(prompt, str) or not prompt.strip():
            raise AgentStateError("prompt must not be empty")
        record = await self._record(agent_id)
        session_id = await self._context_session(record, context.context_id)
        if self.context_store is not None:
            # The durable OAW checkpoint owns replay. Keep ADK's transient event
            # log bounded between invocations without discarding session state.
            session_args = dict(app_name=self._app_name, user_id=self._user_id(agent_id), session_id=session_id)
            previous = await self._sessions.get_session(**session_args)
            if previous and previous.events:
                await self._sessions.delete_session(**session_args)
                await self._sessions.create_session(**session_args, state=previous.state)

        async with record.lock:
            record.config = config
            record.last_error = None
        run_id = context.run_id
        final_text = ""
        run_secret = None
        managed = None
        try:
            definitions = tuple(await self._provider.list_tools(agent_id))
            selected_model = self._adk_model(record.config.model)
            from .typesafe import TypeSafeModel, execute_typesafe
            if isinstance(selected_model, TypeSafeModel):
                run_secret = selected_model.api_key
                from backend.security.redaction import redact
                async with aclosing(execute_typesafe(
                    self._provider, config, context, selected_model, definitions
                )) as stream:
                    async for event in stream:
                        yield AgentEvent(
                            event.agent_id, event.run_id, event.type,
                            redact(dict(event.payload), [run_secret]),
                            timestamp=event.timestamp, run_status=event.run_status,
                        )
                return
            capability_invocations = 0
            database = getattr(self.context_store, "database", None)
            from backend.runs.tool_receipts import RunToolReceipts
            receipt_baseline = (len(RunToolReceipts(database).list_run(run_id))
                                if database is not None else 0)
            provider = self._provider

            class _TrackedProvider:
                async def list_tools(self, selected_agent_id: str):
                    return await provider.list_tools(selected_agent_id)

                async def invoke_tool(self, selected_agent_id: str, capability_id: str,
                                      arguments: Mapping[str, Any]):
                    nonlocal capability_invocations
                    capability_invocations += 1
                    return await provider.invoke_tool(selected_agent_id, capability_id, arguments)

            tools = build_scoped_tool_callables(_TrackedProvider(), agent_id, definitions)
            run_secret = getattr(selected_model, "_additional_args", {}).get("api_key")
            from backend.runs.model_checkpoints import RunModelCheckpoints
            checkpoint_store = (RunModelCheckpoints(database, run_id, api_key=run_secret)
                                if database is not None else None)
            trace_queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
            trace = ModelTrace(trace_queue, run_secret, run_attempt=_network_retry,
                               checkpoint_store=checkpoint_store)
            traced = trace.callbacks("agent")
            if self.context_store is not None:
                from google.adk.models import LLMRegistry
                from .context import ManagedContext
                model = LLMRegistry.new_llm(selected_model) if isinstance(selected_model, str) else selected_model
                selected_model = model
                managed = ManagedContext(self.context_store, agent_id, context.context_id or "",
                                         run_id, model, prompt.strip(),
                                         budget=self._context_budget(record.config.model, model.model))
            async def before_model(callback_context: Any, llm_request: Any) -> None:
                if managed is not None:
                    trace.begin("agent", llm_request, phase="context")
                    await managed.before_model(callback_context, llm_request)
                    trace.model_ready("agent", llm_request)
                else:
                    await traced["before_model_callback"](callback_context, llm_request)

            async def after_model(callback_context: Any, llm_response: Any) -> None:
                if managed is not None:
                    await managed.after_model(callback_context, llm_response)
                await traced["after_model_callback"](callback_context, llm_response)

            context_options = {"before_model_callback": before_model,
                               "after_model_callback": after_model}
            if managed is not None:
                context_options["include_contents"] = "none"
            from .request_recovery import recoverable_model
            selected_model = recoverable_model(selected_model, trace, "agent")
            agent = self._adk.Agent(
                name=self._adk_agent_name(agent_id),
                description=record.config.name,
                model=selected_model,
                instruction=record.config.system_instruction,
                tools=tools,
                **context_options,
            )
            app = self._adk.App(name=self._app_name, root_agent=agent)
            message = self._adk.types.Content(
                role="user",
                parts=[self._adk.types.Part.from_text(text=prompt.strip())],
            )
            pending_call_ids: dict[str, list[str]] = {}
            completed_tool_count = 0
            final_response_seen = False
            auto_retry = False
            async with self._adk.Runner(
                app=app, session_service=self._sessions
            ) as runner:
                from google.adk.agents.run_config import RunConfig, StreamingMode

                stream = runner.run_async(
                    user_id=self._user_id(agent_id), session_id=session_id,
                    new_message=message,
                    run_config=RunConfig(streaming_mode=StreamingMode.SSE),
                )
                try:
                    async for kind, item in multiplex_adk_events(stream, trace_queue, trace):
                        if kind == "progress":
                            yield AgentEvent(agent_id, run_id, AgentEventType.PROGRESS, item)
                            continue
                        if getattr(item, "partial", False):
                            partial_content = getattr(item, "content", None)
                            trace.note_activity("agent", partial_content)
                            trace.observe_partial("agent", partial_content)
                            continue
                        if managed is not None:
                            managed.observe(item)
                        if item.is_final_response():
                            final_response_seen = True
                        async for translated in self._translate_event(record, run_id, item):
                            if translated.type == AgentEventType.TOOL_STARTED:
                                call_id = translated.payload.get("call_id")
                                if isinstance(call_id, str) and call_id:
                                    pending_call_ids.setdefault(str(translated.payload["name"]), []).append(call_id)
                            elif translated.type == AgentEventType.TOOL_COMPLETED:
                                completed_tool_count += 1
                                candidates = pending_call_ids.get(str(translated.payload["name"]), [])
                                call_id = translated.payload.get("call_id")
                                if call_id in candidates:
                                    candidates.remove(call_id)
                                elif not call_id and candidates:
                                    candidates.pop(0)
                            elif translated.type == AgentEventType.MESSAGE:
                                final_text = str(translated.payload.get("text", final_text))
                            yield translated
                except Exception as exc:
                    waiting = trace.waiting()
                    exhausted = getattr(exc, "request_recovery_exhausted", False)
                    if waiting and waiting.get("phase") == "model" and (exhausted or model_stream_disconnected(exc)):
                        preview = trace.interrupted_reasoning("agent")
                        if preview is not None:
                            yield AgentEvent(agent_id, run_id, AgentEventType.PROGRESS, preview)
                        for tool_name, call_ids in pending_call_ids.items():
                            for call_id in call_ids:
                                yield AgentEvent(agent_id, run_id, AgentEventType.TOOL_COMPLETED, {
                                    "name": tool_name, "call_id": call_id,
                                    "success": False,
                                    "response": {"error": "Interrupted before a confirmed tool result; outcome unknown"},
                                })
                        from backend.runs.recovery import recovery_state
                        classification, recovery_receipts = await recovery_state(
                            database, getattr(provider, "services", None), agent_id, run_id,
                            receipt_baseline=receipt_baseline,
                            capability_invocations=capability_invocations,
                        )
                        if not exhausted and classification == "read_only" and _network_retry == 0:
                            auto_retry = True
                            yield AgentEvent(agent_id, run_id, AgentEventType.PROGRESS, {
                                "kind": "model_stream_retry", "role": "agent",
                                "model_request": waiting["model_request"],
                                "text": "Agent model connection interrupted; no mutating capability was dispatched. Restarting once from retained context.",
                            })
                        else:
                            yield AgentEvent(agent_id, run_id, AgentEventType.PROGRESS, {
                                "kind": "model_stream_interrupted", "role": "agent",
                                "model_request": waiting["model_request"],
                                "completed_tool_count": completed_tool_count,
                                "recovery_classification": classification,
                                "recovery_receipts": recovery_receipts,
                                "text": "Agent model connection interrupted. Inspect completed actions before continuing.",
                            })
                            if exhausted:
                                raise
                            raise AgentRuntimeError(
                                "Model stream disconnected after a partial response. Earlier tools may have changed resources; inspect current state before continuing."
                            ) from exc
                    else:
                        raise

            if auto_retry:
                session_args = dict(app_name=self._app_name,
                                    user_id=self._user_id(agent_id), session_id=session_id)
                previous = await self._sessions.get_session(**session_args)
                await self._sessions.delete_session(**session_args)
                await self._sessions.create_session(**session_args,
                                                    state=previous.state if previous else {})
                async with aclosing(self._execute(config, context, runtime_input, _network_retry=1)) as events:
                    async for event in events:
                        yield event
                return

            if not final_response_seen:
                raise AgentRuntimeError(trace.missing_final_error())

            yield AgentEvent(
                agent_id,
                run_id,
                AgentEventType.COMPLETED,
                {"text": final_text},
                run_status=RunStatus.SUCCEEDED,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error_message = _runtime_error_message(exc)
            if run_secret:
                from backend.security.redaction import redact
                error_message = redact(error_message, [run_secret])
            raise RuntimeError(error_message) from None

    async def _translate_event(
        self, record: AgentRecord, run_id: str, event: Any
    ) -> AsyncIterator[AgentEvent]:
        content = getattr(event, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            function_call = getattr(part, "function_call", None)
            if function_call is not None:
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

    async def stop(self, run_id: str) -> None:
        del run_id

    async def get_agent(self, agent_id: str) -> AgentInfo:
        return (await self._record(agent_id)).info()

    async def _record(self, agent_id: str) -> AgentRecord:
        async with self._records_lock:
            try:
                return self._records[agent_id]
            except KeyError as exc:
                raise AgentNotFoundError(f"agent not found: {agent_id}") from exc

    async def _context_session(
        self, record: AgentRecord, context_id: str | None
    ) -> str:
        if not context_id:
            return record.session_id
        key = (record.config.agent_id, context_id)
        async with self._records_lock:
            existing = self._context_sessions.get(key)
            if existing is not None:
                return existing
            session = await self._sessions.create_session(
                app_name=self._app_name,
                user_id=self._user_id(record.config.agent_id),
                session_id=self._session_id(record.config.agent_id, context_id),
            )
            self._context_sessions[key] = session.id
            return session.id

    def _context_budget(self, configured_model: str, resolved_model: str) -> ContextBudget:
        limits = self.model_connections.context_limits(configured_model) if self.model_connections else None
        if limits is not None:
            window, output = limits
            return ContextBudget(window, output, max_output=output)
        return ContextBudget.for_model(resolved_model)

    def _adk_model(self, configured_model: str) -> Any:
        """Return a configured LiteLlm object only when ADK selects that adapter."""

        from backend.security.model_connections import MODEL_REF_PREFIX
        if configured_model == "oaw:default":
            configured_model = self.model_connections.read().default_model if self.model_connections else None
            if not configured_model:
                raise AgentStateError("Choose a default model and configure its connection in Settings / Models.")
        if configured_model.startswith(MODEL_REF_PREFIX):
            if self.model_connections is None:
                raise AgentStateError("Model connections are not configured on this runtime")
            adapter, model_id, base_url, api_key = self.model_connections.resolve(configured_model)
            if adapter == "typesafe":
                from .typesafe import TypeSafeModel
                return TypeSafeModel(model=model_id, base_url=base_url, api_key=api_key or "")
            from google.adk.models.lite_llm import LiteLlm
            if adapter == "legacy":
                from google.adk.models import LLMRegistry
                if not isinstance(LLMRegistry.new_llm(model_id), LiteLlm):
                    return model_id
            else:
                model_id = model_id if model_id.startswith(adapter + "/") else adapter + "/" + model_id
            options = {}
            if base_url:
                options["api_base"] = base_url
            if api_key:
                options["api_key"] = api_key
            from .resilient_litellm import ResilientLiteLlm
            return ResilientLiteLlm(model_id, **options)

        from google.adk.models import LLMRegistry
        from google.adk.models.lite_llm import LiteLlm

        resolved = LLMRegistry.new_llm(configured_model)
        if isinstance(resolved, LiteLlm):
            connection = self.model_connections.legacy_options() if self.model_connections else None
            from .resilient_litellm import ResilientLiteLlm
            return ResilientLiteLlm(configured_model, **(connection if connection is not None else self._litellm_connection))
        return configured_model

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
    def _session_id(cls, agent_id: str, context_id: str | None = None) -> str:
        scope = agent_id if context_id is None else f"{agent_id}:{context_id}"
        return f"agent-session-{cls._digest(scope)}"


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


def _runtime_error_message(error: BaseException) -> str:
    """Expose the most actionable provider failure, not ADK's wrapper error."""

    pending = [error]
    seen: set[int] = set()
    messages: list[str] = []
    while pending:
        current = pending.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        message = str(current).strip()
        if message:
            messages.append(message)
            if "missing credentials" in message.lower() or "api_key" in message.lower():
                return message
        for nested in (
            current.__cause__,
            current.__context__,
            getattr(current, "error", None),
        ):
            if isinstance(nested, BaseException):
                pending.append(nested)
    return messages[0] if messages else type(error).__name__

