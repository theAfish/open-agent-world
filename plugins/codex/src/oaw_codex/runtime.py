"""OAW execution adapter; provider sessions stay outside host world storage."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from pydantic import TypeAdapter

from open_agent_world.plugin_api import (
    AgentCapabilityProvider, AgentConfig, AgentConfigurationError, AgentEvent,
    AgentEventType, AgentInfo, AgentNotFoundError, AgentRuntimeError, AgentStateError, AgentStatus,
    InvocationContext, RuntimeInput, RuntimeProvider, ResourceValidationError,
    codex_tool_content,
)

from open_agent_world.plugin_api.tool_execution import execute_tool

from .transport import AppServer, AppServerRequestError
from .discovery import discover


BRIDGE_INSTRUCTION = """You are an Agent in Open Agent World (OAW).
Use oaw_list_tools to discover currently connected world resources and actions.
Use oaw_invoke_tool for those actions; capability IDs are scoped and may be revoked.
The local cwd is your coding workspace. Native file/command access uses Codex's own
sandbox, not an OAW Sandbox card. Use the OAW sandbox tool when asked to execute in
an attached Sandbox. Do not edit OAW's database or call its HTTP API to bypass tools.
This integration cannot display interactive approval or question dialogs. Ask the
user questions in your final message and continue when they reply in OAW.
"""

DYNAMIC_TOOLS = [
    {"type": "function", "name": "oaw_list_tools",
     "description": "List this Agent's currently authorized OAW tools, capability IDs and argument schemas.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"type": "function", "name": "oaw_invoke_tool",
     "description": "Invoke an OAW capability from oaw_list_tools. Authorization is checked again on every call.",
     "inputSchema": {"type": "object", "properties": {
         "capability_id": {"type": "string"},
         "arguments": {"type": "object", "additionalProperties": True},
     }, "required": ["capability_id", "arguments"], "additionalProperties": False}},
]


class CodexRuntime(RuntimeProvider):
    def __init__(self, capability_provider: AgentCapabilityProvider, *,
                 state_directory: str | Path | None = None,
                 workspace_root: Callable[[], Path] | None = None,
                 server_command: list[str] | None = None) -> None:
        self.capabilities = capability_provider
        self.records: dict[str, AgentInfo] = {}
        self.active: dict[str, tuple[str, AppServer]] = {}
        self.server_command = server_command
        self._discoveries: dict[tuple[str, str], tuple[float, dict[str, str]]] = {}
        root = state_directory or os.environ.get("OAW_CODEX_STATE_DIR")
        if root is None:
            root = Path(os.environ.get("OPEN_AGENT_WORLD_DATA_ROOT", ".open-agent-world")) / "codex"
        self.database = Path(root).resolve() / "sessions.sqlite3"
        self.workspace_root = workspace_root or (lambda: self.database.parent)

    def _settings(self, config: AgentConfig, *, require_workspace: bool = True) -> tuple[str, str]:
        raw = config.provider_config.get("workspace_path", "")
        if not isinstance(raw, str) or (raw.strip() and not Path(raw.strip()).is_absolute()):
            raise AgentConfigurationError("Codex Project folder must be an absolute path, or blank to use the default workspace")
        raw = raw.strip()
        path = Path(raw).resolve() if raw else None
        if raw and not path.is_dir():
            raise AgentConfigurationError(f"Codex workspace does not exist: {path}")
        if not raw and require_workspace:
            try:
                path = (self.workspace_root() / "codex-workspace").resolve()
            except (OSError, ValueError) as exc:
                raise AgentConfigurationError(f"Cannot resolve the default Codex workspace: {exc}") from exc
        sandbox = config.provider_config.get("codex_sandbox", "workspace-write")
        if sandbox not in {"read-only", "workspace-write"}:
            raise AgentConfigurationError("codex_sandbox must be read-only or workspace-write")
        if config.max_concurrent_runs != 1:
            raise AgentConfigurationError("Codex Agents currently require max_concurrent_runs=1")
        if not config.model.strip():
            raise AgentConfigurationError("Set model to default or a Codex model ID")
        return str(path) if path else "", sandbox

    async def create_agent(self, config: AgentConfig) -> AgentInfo:
        self._settings(config, require_workspace=False)
        if config.agent_id in self.records:
            raise AgentStateError(f"agent already exists: {config.agent_id}")
        info = AgentInfo(config=config, status=AgentStatus.IDLE, session_id=f"codex-{config.agent_id}")
        self.records[config.agent_id] = info
        return info

    async def update_agent(self, config: AgentConfig) -> AgentInfo:
        await self.get_agent(config.agent_id)
        self._settings(config, require_workspace=False)
        if any(agent == config.agent_id for agent, _ in self.active.values()):
            raise AgentStateError("cannot update a running Codex Agent")
        info = AgentInfo(config=config, status=AgentStatus.IDLE, session_id=f"codex-{config.agent_id}")
        self.records[config.agent_id] = info
        return info

    async def delete_agent(self, agent_id: str) -> None:
        await self.get_agent(agent_id)
        for run_id, (owner, _) in tuple(self.active.items()):
            if owner == agent_id:
                await self.stop(run_id)
        if self.database.exists():
            with sqlite3.connect(self.database) as db:
                db.execute("DELETE FROM sessions WHERE agent_id = ?", (agent_id,))
        self.records.pop(agent_id, None)

    async def get_agent(self, agent_id: str) -> AgentInfo:
        try:
            record = self.records[agent_id]
        except KeyError as exc:
            raise AgentNotFoundError(f"agent not found: {agent_id}") from exc
        try:
            connection = await self._discover(record.config)
            details = {**connection, "available": True, "workspace": self._settings(record.config)[0]}
        except AgentRuntimeError as exc:
            details = {"available": False, "error": str(exc)}
        return replace(record, details=details)

    async def _discover(self, config: AgentConfig) -> dict[str, str]:
        if self.server_command is not None:
            return {"source": "test", "executable": self.server_command[0], "version": "test fixture"}
        key = (str(config.provider_config.get("client_source", "auto")), str(config.provider_config.get("codex_command", "")))
        cached = self._discoveries.get(key)
        if cached and time.monotonic() - cached[0] < 15:
            return cached[1]
        result = await asyncio.to_thread(discover, *key)
        self._discoveries[key] = (time.monotonic(), result)
        return result

    def _session(self, agent: str, scope: str, thread: str | None = None) -> str | None:
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.database) as db:
            db.execute("CREATE TABLE IF NOT EXISTS sessions "
                       "(agent_id TEXT, scope TEXT, thread_id TEXT, PRIMARY KEY(agent_id, scope))")
            if thread is not None:
                db.execute("INSERT OR REPLACE INTO sessions VALUES (?, ?, ?)", (agent, scope, thread))
                return thread
            row = db.execute("SELECT thread_id FROM sessions WHERE agent_id=? AND scope=?", (agent, scope)).fetchone()
            return row[0] if row else None

    async def _command(self, config: AgentConfig) -> list[str]:
        if self.server_command is not None:
            return list(self.server_command)
        connection = await self._discover(config)
        from open_agent_world.plugin_api import ExecutableEnvironmentManager, ExecutableEnvironmentSpec
        get_root = getattr(self.capabilities, 'managed_environment_root', None)
        manager = ExecutableEnvironmentManager(get_root() if get_root else None)
        spec = ExecutableEnvironmentSpec('openai.codex', 'client', (), Path(__file__).parent,
            ('codex',), ('--version',), '^codex-cli ', True)
        # Adopt only during execution. Desktop/PATH/manual selection remains
        # external ownership; status discovery never installs or creates state.
        handle = await asyncio.to_thread(manager.external, spec, path=connection['executable'], verify=True)
        return [handle.executable, "app-server", "--listen", "stdio://"]

    async def _tool(self, agent_id: str, name: str, arguments: dict) -> Any:
        if not isinstance(arguments, dict):
            raise ResourceValidationError("Tool arguments must be an object")
        if name == "oaw_list_tools":
            definitions = await self.capabilities.list_tools(agent_id)
            return [{
                "capability_id": tool.capability_id, "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
                "parameters": [{"name": p.name, "description": p.description,
                                "required": p.required,
                                "schema": ((tool.input_schema or {}).get("properties", {}).get(p.name)
                                           or TypeAdapter(p.python_type).json_schema())}
                               for p in tool.parameters],
            } for tool in definitions]
        if name == "oaw_invoke_tool":
            capability_id, values = arguments.get("capability_id"), arguments.get("arguments")
            if not isinstance(capability_id, str) or not isinstance(values, dict):
                raise ResourceValidationError("capability_id must be a string and arguments an object")
            return await self.capabilities.invoke_tool(agent_id, capability_id, values)
        raise ResourceValidationError(f"Unknown OAW tool: {name}")

    async def execute(self, config: AgentConfig, context: InvocationContext,
                      runtime_input: RuntimeInput) -> AsyncIterator[AgentEvent]:
        await self.get_agent(config.agent_id)
        cwd, sandbox = self._settings(config)
        if not str(config.provider_config.get("workspace_path", "")).strip():
            try:
                Path(cwd).mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise AgentConfigurationError(f"Cannot create the default Codex workspace {cwd}: {exc}") from exc
        if not runtime_input.prompt.strip():
            raise AgentStateError("prompt must not be empty")
        if any(owner == config.agent_id for owner, _ in self.active.values()):
            raise AgentStateError("Codex Agent already has an active Run")
        agent_id, run_id = config.agent_id, context.run_id
        input_lock = asyncio.Lock()
        next_input_check = 0.0
        input_rejected = False

        async def steer_pending(*, force=False):
            nonlocal next_input_check, input_rejected
            if not thread_id or not turn_id or input_rejected or not hasattr(self.capabilities, 'pending_conversation_input'):
                return
            async with input_lock:
                if input_rejected or (not force and time.monotonic() < next_input_check):
                    return
                next_input_check = time.monotonic() + .1
                pending = await self.capabilities.pending_conversation_input(agent_id, run_id)
                if not pending:
                    return
                try:
                    await server.request('turn/steer', {
                        'threadId': thread_id, 'expectedTurnId': turn_id,
                        'input': [{'type': 'text', 'text': item['text'], 'text_elements': []} for item in pending],
                    })
                except AppServerRequestError:
                    # A just-completed turn (or an older server) can reject the
                    # steer. Keep input queued for the next turn, never drop it.
                    input_rejected = True
                    return
                await self.capabilities.acknowledge_conversation_input(
                    agent_id, run_id, [item['id'] for item in pending])

        def event(kind: AgentEventType, payload: dict, status: str | None = None) -> AgentEvent:
            return AgentEvent(agent_id, run_id, kind, payload, run_status=status)

        async def handle(method: str, params: dict) -> dict:
            if method != "item/tool/call":
                raise AgentRuntimeError(
                    f"Codex requested interactive input ({method}). This preview supports "
                    "sandboxed non-interactive runs; reply through the OAW conversation instead."
                )
            name, arguments = params.get("tool", ""), params.get("arguments", {})
            call_id = params.get("callId")
            await server.events.put({"oaw_event": event(AgentEventType.TOOL_STARTED, {
                "name": name, "call_id": call_id, "arguments": arguments,
            })})
            outcome = await execute_tool(
                lambda: self._tool(agent_id, name, arguments),
                serialize=codex_tool_content,
            )
            content = outcome.response
            # Queue user input before releasing this tool result to Codex.
            await steer_pending(force=True)
            text, success = content[0]["text"], outcome.ok
            await server.events.put({"oaw_event": event(AgentEventType.TOOL_COMPLETED, {
                "name": name, "call_id": call_id, "response": text[:16000], "success": success,
            })})
            return {"contentItems": content, "success": success}

        server = AppServer(await self._command(config), cwd, handle)
        self.active[run_id] = (agent_id, server)
        thread_id: str | None = None
        turn_id: str | None = None
        complete = False
        try:
            await server.start()
            # Workspace or sandbox changes start a fresh thread. OAW sessions remain isolated.
            scope = hashlib.sha256(json.dumps([
                context.context_id or "agent", cwd, sandbox,
            ]).encode()).hexdigest()
            continue_session = config.provider_config.get("session_mode", "continue") == "continue"
            thread_id = self._session(agent_id, scope) if continue_session else None
            params: dict[str, Any] = {
                "cwd": cwd, "sandbox": sandbox, "approvalPolicy": "never",
                "developerInstructions": config.system_instruction + "\n\n" + BRIDGE_INSTRUCTION,
            }
            if config.model != "default":
                params["model"] = config.model
            if thread_id:
                params["threadId"] = thread_id
                response = await server.request("thread/resume", params)
            else:
                params["dynamicTools"] = DYNAMIC_TOOLS
                response = await server.request("thread/start", params)
                thread_id = response["thread"]["id"]
                if continue_session:
                    self._session(agent_id, scope, thread_id)
            self.records[agent_id] = replace(self.records[agent_id], config=config, session_id=thread_id)
            yield event(AgentEventType.STATUS_CHANGED, {
                "status": "running", "thread_id": thread_id, "workspace_path": cwd,
            })
            turn_params = {
                "threadId": thread_id,
                "input": [{"type": "text", "text": runtime_input.prompt, "text_elements": []}],
                "summary": "concise",
            }
            effort = config.provider_config.get("reasoning_effort", "default")
            if effort != "default":
                turn_params["effort"] = effort
            response = await server.request("turn/start", turn_params)
            turn_id = response["turn"]["id"]
            texts: dict[str, str] = {}
            summaries: dict[str, dict[int, str]] = {}
            final_text = ""
            while True:
                await steer_pending()
                try:
                    message = await asyncio.wait_for(server.events.get(), timeout=.1)
                except TimeoutError:
                    continue
                if isinstance(message, Exception):
                    raise message
                if message.get('oaw_input'):
                    await steer_pending(force=True)
                    continue
                if "oaw_event" in message:
                    yield message["oaw_event"]
                    continue
                method, data = message.get("method"), message.get("params", {})
                if data.get("threadId", thread_id) != thread_id or data.get("turnId", turn_id) != turn_id:
                    continue
                if method == "item/agentMessage/delta":
                    item_id = data["itemId"]
                    texts[item_id] = texts.get(item_id, "") + data["delta"]
                    yield event(AgentEventType.MESSAGE, {"text": texts[item_id], "final": False, "provider_message_id": item_id})
                elif method == "item/reasoning/summaryTextDelta":
                    item_id = data["itemId"]
                    parts = summaries.setdefault(item_id, {})
                    index = data.get("summaryIndex", 0)
                    parts[index] = parts.get(index, "") + data["delta"]
                    yield event(AgentEventType.PROGRESS, {"kind": "model_reasoning", "role": "agent",
                        "provider_message_id": f"codex-summary:{turn_id}:{item_id}", "streaming": True,
                        "text": "\n\n".join(parts[index] for index in sorted(parts))})
                elif method in {"item/started", "item/completed"}:
                    item = data.get("item", {})
                    kind = item.get("type")
                    if kind == "agentMessage" and method == "item/completed":
                        final_text = item.get("text", "")
                        yield event(AgentEventType.MESSAGE, {"text": final_text, "final": True, "provider_message_id": item.get("id")})
                    elif kind == "reasoning" and method == "item/completed":
                        item_id = item.get("id")
                        parts = summaries.pop(item_id, {})
                        summary = item.get("summary")
                        text = "\n\n".join(part for part in summary if isinstance(part, str)) if isinstance(summary, list) and summary else "\n\n".join(parts[index] for index in sorted(parts))
                        if item_id and text:
                            # Only readable provider summaries cross this boundary;
                            # raw reasoning content/textDelta stays provider-owned.
                            yield event(AgentEventType.PROGRESS, {"kind": "model_reasoning", "role": "agent",
                                "provider_message_id": f"codex-summary:{turn_id}:{item_id}", "streaming": False, "text": text})
                    elif kind in {"commandExecution", "fileChange", "mcpToolCall", "webSearch"}:
                        started = method == "item/started"
                        if started:
                            yield event(AgentEventType.PROGRESS, {
                                "kind": "status",
                                "text": f"Running {kind}",
                            })
                        yield event(AgentEventType.TOOL_STARTED if started else AgentEventType.TOOL_COMPLETED, {
                            "name": kind, "call_id": item.get("id"),
                            "arguments" if started else "response": {
                                key: item[key] for key in ("command", "cwd", "status", "exitCode", "changes", "tool", "query",
                                    "aggregatedOutput", "arguments", "result", "error")
                                if key in item
                            },
                        })
                elif method == "turn/completed":
                    turn = data["turn"]
                    status = turn["status"]
                    if status == "failed":
                        raise AgentRuntimeError((turn.get("error") or {}).get("message", "Codex turn failed"))
                    if status not in {"completed", "interrupted"}:
                        raise AgentRuntimeError(f"Unexpected Codex turn status: {status}")
                    complete = True
                    # Release local process ownership before the host sees a
                    # terminal Run and admits another turn for this Agent.
                    await server.close()
                    self.active.pop(run_id, None)
                    yield event(AgentEventType.COMPLETED if status == "completed" else AgentEventType.STOPPED,
                                {"text": final_text, "thread_id": thread_id},
                                "succeeded" if status == "completed" else "cancelled")
                    return
                elif method == "error" and not data.get("willRetry", False):
                    raise AgentRuntimeError((data.get("error") or {}).get("message", "Codex execution failed"))
        finally:
            try:
                if thread_id and turn_id and not complete:
                    try:
                        await server.request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id}, timeout=3)
                    except Exception:
                        pass
                await server.close()
            finally:
                self.active.pop(run_id, None)

    async def stop(self, run_id: str) -> None:
        active = self.active.get(run_id)
        if active:
            await active[1].events.put(AgentRuntimeError("Codex Run stopped"))
            await active[1].close()

    async def notify_input(self, run_id: str) -> None:
        active = self.active.get(run_id)
        if active:
            await active[1].events.put({'oaw_input': True})
