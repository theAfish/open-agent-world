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
from typing import Any, AsyncIterator

from pydantic import TypeAdapter

from open_agent_world.plugin_api import (
    AgentCapabilityProvider, AgentConfig, AgentConfigurationError, AgentEvent,
    AgentEventType, AgentInfo, AgentNotFoundError, AgentRuntimeError, AgentStateError, AgentStatus,
    InvocationContext, RuntimeInput, RuntimeProvider,
)

from .transport import AppServer
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

    @staticmethod
    def _settings(config: AgentConfig, *, require_workspace: bool = True) -> tuple[str, str]:
        raw = config.provider_config.get("workspace_path", "")
        if not isinstance(raw, str) or (raw and not Path(raw).is_absolute()) or (require_workspace and not raw.strip()):
            raise AgentConfigurationError("Codex requires an absolute workspace_path on its Agent card")
        path = Path(raw).resolve() if raw else None
        if path and not path.is_dir():
            raise AgentConfigurationError(f"Codex workspace does not exist: {path}")
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
            details = {**connection, "available": True, "workspace": record.config.provider_config.get("workspace_path", "")}
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
        return [connection["executable"], "app-server", "--listen", "stdio://"]

    async def _tool(self, agent_id: str, name: str, arguments: dict) -> Any:
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
                raise ValueError("capability_id must be a string and arguments an object")
            return await self.capabilities.invoke_tool(agent_id, capability_id, values)
        raise ValueError(f"Unknown OAW tool: {name}")

    async def execute(self, config: AgentConfig, context: InvocationContext,
                      runtime_input: RuntimeInput) -> AsyncIterator[AgentEvent]:
        await self.get_agent(config.agent_id)
        cwd, sandbox = self._settings(config)
        if not runtime_input.prompt.strip():
            raise AgentStateError("prompt must not be empty")
        if any(owner == config.agent_id for owner, _ in self.active.values()):
            raise AgentStateError("Codex Agent already has an active Run")
        agent_id, run_id = config.agent_id, context.run_id

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
            try:
                result = await self._tool(agent_id, name, arguments)
                text = json.dumps(result, ensure_ascii=False)
                success = True
            except Exception as exc:
                text, success = str(exc), False
            await server.events.put({"oaw_event": event(AgentEventType.TOOL_COMPLETED, {
                "name": name, "call_id": call_id, "response": text[:16000], "success": success,
            })})
            return {"contentItems": [{"type": "inputText", "text": text}], "success": success}

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
            }
            effort = config.provider_config.get("reasoning_effort", "default")
            if effort != "default":
                turn_params["effort"] = effort
            response = await server.request("turn/start", turn_params)
            turn_id = response["turn"]["id"]
            texts: dict[str, str] = {}
            final_text = ""
            while True:
                message = await server.events.get()
                if isinstance(message, Exception):
                    raise message
                if "oaw_event" in message:
                    yield message["oaw_event"]
                    continue
                method, data = message.get("method"), message.get("params", {})
                if data.get("threadId", thread_id) != thread_id or data.get("turnId", turn_id) != turn_id:
                    continue
                if method == "item/agentMessage/delta":
                    item_id = data["itemId"]
                    texts[item_id] = texts.get(item_id, "") + data["delta"]
                    yield event(AgentEventType.MESSAGE, {"text": texts[item_id], "final": False})
                elif method in {"item/started", "item/completed"}:
                    item = data.get("item", {})
                    kind = item.get("type")
                    if kind == "agentMessage" and method == "item/completed":
                        final_text = item.get("text", "")
                        yield event(AgentEventType.MESSAGE, {"text": final_text, "final": True})
                    elif kind in {"commandExecution", "fileChange", "mcpToolCall", "webSearch"}:
                        started = method == "item/started"
                        yield event(AgentEventType.TOOL_STARTED if started else AgentEventType.TOOL_COMPLETED, {
                            "name": kind, "call_id": item.get("id"),
                            "arguments" if started else "response": {
                                key: item[key] for key in ("command", "cwd", "status", "exitCode", "changes", "tool", "query")
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
